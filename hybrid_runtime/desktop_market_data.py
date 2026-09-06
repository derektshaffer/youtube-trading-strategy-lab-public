"""Strict, read-only desktop boundary around the existing Alpaca provider.

No retries, feed substitution, strategy changes, or credentials are introduced.
Errors intentionally do not inherit AppError: optional web chart fallbacks must
not swallow a desktop provider failure and publish a complete-looking scan.
"""
from urllib.error import HTTPError, URLError


class MarketDataUnavailable(RuntimeError):
    pass


def provider_failure(exc):
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPError):
            code = current.code
            if code in (401, 403):
                return f"Alpaca authorization failed ({code}). Check Data Connections and feed permissions."
            if code == 429:
                return "Alpaca rate limit reached (429). Wait before trying again; no automatic retry was started."
            return f"Alpaca market data temporarily unavailable (HTTP {code}). Try again later."
        if isinstance(current, TimeoutError):
            return "Alpaca market-data request timed out. Try again."
        if isinstance(current, (URLError, ConnectionError, OSError)):
            return "Alpaca market data could not be reached. Check the network and try again."
        current = current.__cause__ or current.__context__
    # Never copy an arbitrary provider body, URL, header, or exception into a job.
    return "Alpaca market data unavailable or invalid. Check the selected feed and try again."


class DesktopMarketData:
    def __init__(self, provider, *, cancelled=lambda: False):
        self.provider = provider
        self.live_feed = getattr(provider, "live_feed", "unknown")
        self.historical_feed = getattr(provider, "historical_feed", "unknown")
        self.cancelled = cancelled
        self.snapshot_provenance = {}

    def _call(self, name, *args, **kwargs):
        if self.cancelled():
            raise MarketDataUnavailable("Market-data operation cancelled.")
        try:
            result = getattr(self.provider, name)(*args, **kwargs)
        except Exception as exc:
            raise MarketDataUnavailable(provider_failure(exc)) from None
        if self.cancelled():
            raise MarketDataUnavailable("Market-data operation cancelled.")
        return result

    def movers(self, **kwargs):
        return self._call("movers", **kwargs)

    def most_active(self, **kwargs):
        return self._call("most_active", **kwargs)

    def news(self, *args, **kwargs):
        return self._call("news", *args, **kwargs)

    def snapshots(self, symbols):
        from youtube_strategy_engine import safe_float
        result = self._call("snapshots", symbols)
        if not isinstance(result, dict):
            raise MarketDataUnavailable("Alpaca returned malformed snapshot data.")
        for symbol in symbols:
            snapshot = result.get(symbol)
            if not isinstance(snapshot, dict) or not snapshot:
                raise MarketDataUnavailable(f"Alpaca snapshot unavailable for {symbol}; discovery is incomplete.")
            trade = snapshot.get("latestTrade") or snapshot.get("latest_trade") or {}
            daily = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
            quote = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
            if not all(isinstance(x, dict) for x in (trade, daily, quote)):
                raise MarketDataUnavailable("Alpaca returned malformed snapshot fields.")
            trade_price = safe_float(trade.get("p"))
            daily_price = safe_float(daily.get("c"))
            if not ((trade_price and trade_price > 0) or (daily_price and daily_price > 0)):
                raise MarketDataUnavailable(f"Alpaca snapshot has no usable price for {symbol}.")
            use_trade = bool(trade_price and trade_price > 0)
            self.snapshot_provenance[symbol] = {
                "price_source": "trade snapshot" if use_trade else "historical daily-close fallback",
                "price_timestamp": trade.get("t") if use_trade else daily.get("t"),
                "feed": self.live_feed,
                "provider": "Alpaca",
            }
        return {symbol: result[symbol] for symbol in symbols}

    def bars(self, symbols, **kwargs):
        from .market_cache import normalize_provider_bars
        result = self._call("bars", symbols, **kwargs)
        if not isinstance(result, dict) or any(not isinstance(result.get(s), list) for s in symbols):
            self._invalidate_rejected_history(symbols, kwargs)
            raise MarketDataUnavailable("Alpaca returned malformed or missing candle collections.")
        for symbol in symbols:
            for row in result[symbol]:
                if not isinstance(row, dict) or not normalize_provider_bars([row]):
                    self._invalidate_rejected_history(symbols, kwargs)
                    raise MarketDataUnavailable(f"Alpaca returned incomplete or invalid candles for {symbol}.")
        return {symbol: result[symbol] for symbol in symbols}

    def _invalidate_rejected_history(self, symbols, request):
        """Discard only this request's invalid shared historical cache entry.

        Alpaca caches its historical prefix before desktop completeness checks.
        A rejected current-day response must not evict an otherwise valid prefix.
        No retry is performed here; the next explicit user request fetches again.
        """
        from datetime import timezone
        from .market_cache import normalize_provider_bars
        import youtube_strategy_engine as engine

        if not hasattr(self.provider, "_history_cache_key"):
            return
        start, end = request.get("start"), request.get("end")
        if start is None or end is None:
            return
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        end = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)
        cutoff = self.provider._history_cache_cutoff_utc()
        historical_end = min(end, cutoff)
        if start >= historical_end:
            return
        clean = engine.parse_symbols(symbols)
        key = self.provider._history_cache_key(
            clean, start=start, end=historical_end,
            timeframe=request.get("timeframe", "1Min"),
            feed=request.get("feed") or self.historical_feed,
            adjustment=str(request.get("adjustment") or "split").strip().lower(),
            max_pages=request.get("max_pages", 15),
        )
        with engine._ALPACA_BAR_HISTORY_CACHE_LOCK:
            entry = engine._ALPACA_BAR_HISTORY_CACHE.get(key)
            if entry is None:
                return
            payload = entry[2]
            invalid = any(
                not isinstance(payload.get(symbol), list)
                or any(not isinstance(row, dict) or not normalize_provider_bars([row])
                       for row in payload.get(symbol, []))
                for symbol in clean
            )
            if invalid:
                engine._ALPACA_BAR_HISTORY_CACHE.pop(key, None)
