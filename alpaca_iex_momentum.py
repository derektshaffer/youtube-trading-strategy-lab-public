"""Alpaca momentum discovery that works on IEX-only market-data plans.

Alpaca's beta movers/most-active screeners are SIP-backed even when the rest of
an application is configured for IEX.  The Explosive Stock Scanner only needs a
ranked candidate universe, so on an IEX plan we derive that universe from active
U.S. equities plus batched IEX snapshots instead of calling the SIP-only
screeners.
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from youtube_strategy_engine import (
    AlpacaMarketData,
    AppError,
    is_research_equity_symbol,
    parse_symbols,
    safe_float,
)

IEX_SNAPSHOT_BATCH_SIZE = 150
IEX_MOMENTUM_CACHE_SECONDS = 60.0
IEX_GAINERS_CACHE_SIZE = 50
IEX_ACTIVE_CACHE_SIZE = 100


def _clean_market_access_error(error: Exception | str) -> AppError:
    """Return an actionable provider error without leaking HTML response bodies."""
    message = str(error or "")
    lowered = message.lower()
    if any(marker in lowered for marker in ("401", "authorization required", "unauthorized")):
        return AppError(
            "Alpaca authentication failed. Check ALPACA_API_KEY and ALPACA_SECRET_KEY "
            "in this app's secrets, then restart the app."
        )
    if any(
        marker in lowered
        for marker in (
            "403",
            "premium_feed_required",
            "subscription",
            "not permit",
            "forbidden",
        )
    ):
        return AppError(
            "The selected Alpaca market-data feed is not included with this account. "
            "Use ALPACA_LIVE_FEED=iex unless the account has a SIP subscription."
        )
    return AppError(f"Alpaca live market data is unavailable: {message[:180]}")


def _snapshot_value(snapshot: dict[str, Any], *names: str) -> dict[str, Any]:
    for name in names:
        value = snapshot.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _snapshot_rank_row(symbol: str, snapshot: dict[str, Any]) -> dict[str, float | str] | None:
    """Extract only fields needed to rank current IEX gainers and activity."""
    daily = _snapshot_value(snapshot, "dailyBar", "daily_bar")
    previous = _snapshot_value(
        snapshot,
        "prevDailyBar",
        "prev_daily_bar",
        "previousDailyBar",
        "previous_daily_bar",
    )
    latest_trade = _snapshot_value(snapshot, "latestTrade", "latest_trade")

    current = safe_float(daily.get("c"))
    if current is None or current <= 0:
        current = safe_float(latest_trade.get("p"))
    if current is None or current <= 0:
        return None

    previous_close = safe_float(previous.get("c"))
    volume = max(0.0, safe_float(daily.get("v"), 0.0) or 0.0)
    day_change_pct = None
    if previous_close is not None and previous_close > 0:
        day_change_pct = (current / previous_close - 1.0) * 100.0

    return {
        "symbol": symbol,
        "price": current,
        "volume": volume,
        "dollar_volume": current * volume,
        "day_change_pct": day_change_pct if day_change_pct is not None else -1.0e12,
    }


class IexCompatibleAlpacaMarketData(AlpacaMarketData):
    """Use Alpaca SIP screeners when available, otherwise rank live IEX snapshots."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        live_feed: str = "iex",
        historical_feed: str = "sip",
    ) -> None:
        super().__init__(api_key, secret_key, live_feed, historical_feed)
        self._iex_momentum_cache_at = 0.0
        self._iex_momentum_cache: tuple[list[str], list[str]] | None = None

    def _active_equity_symbols(self) -> list[str]:
        try:
            assets = self.equity_catalog(status="active")
        except AppError as exc:
            raise _clean_market_access_error(exc) from exc

        symbols: list[str] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if asset.get("tradable") is False:
                continue
            asset_class = str(asset.get("class") or asset.get("asset_class") or "us_equity").lower()
            if asset_class not in {"us_equity", "us equity", ""}:
                continue
            symbol = str(asset.get("symbol") or "").strip().upper()
            if is_research_equity_symbol(symbol):
                symbols.append(symbol)
        return parse_symbols(symbols)

    def _iex_momentum_lists(self) -> tuple[list[str], list[str]]:
        now = monotonic()
        if (
            self._iex_momentum_cache is not None
            and now - self._iex_momentum_cache_at < IEX_MOMENTUM_CACHE_SECONDS
        ):
            return self._iex_momentum_cache

        symbols = self._active_equity_symbols()
        if not symbols:
            raise AppError("Alpaca returned no active U.S. equities for live discovery.")

        rows: list[dict[str, float | str]] = []
        try:
            for start in range(0, len(symbols), IEX_SNAPSHOT_BATCH_SIZE):
                batch = symbols[start : start + IEX_SNAPSHOT_BATCH_SIZE]
                snapshots = self.snapshots(batch)
                for symbol in batch:
                    snapshot = snapshots.get(symbol)
                    if not isinstance(snapshot, dict):
                        continue
                    row = _snapshot_rank_row(symbol, snapshot)
                    if row is not None:
                        rows.append(row)
        except AppError as exc:
            raise _clean_market_access_error(exc) from exc

        if not rows:
            raise AppError(
                "Alpaca IEX returned no current stock snapshots. Try the scan again in a moment."
            )

        gainers_rows = sorted(
            rows,
            key=lambda item: (
                float(item["day_change_pct"]),
                float(item["dollar_volume"]),
            ),
            reverse=True,
        )
        active_rows = sorted(
            rows,
            key=lambda item: (
                float(item["volume"]),
                float(item["dollar_volume"]),
            ),
            reverse=True,
        )
        gainers = [str(item["symbol"]) for item in gainers_rows[:IEX_GAINERS_CACHE_SIZE]]
        active = [str(item["symbol"]) for item in active_rows[:IEX_ACTIVE_CACHE_SIZE]]
        self._iex_momentum_cache = (gainers, active)
        self._iex_momentum_cache_at = now
        return self._iex_momentum_cache

    def movers(self, top: int = 30) -> list[str]:
        cap = max(1, min(IEX_GAINERS_CACHE_SIZE, int(top)))
        if str(self.live_feed or "").strip().lower() == "sip":
            try:
                return super().movers(top=cap)
            except AppError as exc:
                # A stale key or missing SIP entitlement should not dump provider HTML
                # into the app; try the account-compatible IEX path before failing.
                original_feed = self.live_feed
                self.live_feed = "iex"
                try:
                    return self._iex_momentum_lists()[0][:cap]
                except AppError:
                    raise _clean_market_access_error(exc) from exc
                finally:
                    self.live_feed = original_feed
        return self._iex_momentum_lists()[0][:cap]

    def most_active(self, top: int = 30) -> list[str]:
        cap = max(1, min(IEX_ACTIVE_CACHE_SIZE, int(top)))
        if str(self.live_feed or "").strip().lower() == "sip":
            try:
                return super().most_active(top=cap)
            except AppError as exc:
                original_feed = self.live_feed
                self.live_feed = "iex"
                try:
                    return self._iex_momentum_lists()[1][:cap]
                except AppError:
                    raise _clean_market_access_error(exc) from exc
                finally:
                    self.live_feed = original_feed
        return self._iex_momentum_lists()[1][:cap]
