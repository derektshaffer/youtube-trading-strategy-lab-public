"""Persistent incremental market-data cache for the Trading Intelligence desktop.

This module deliberately reuses the existing Alpaca provider implementation. It
adds only a local, fingerprinted artifact layer so repeated desktop analyses do
not download and recompute the same candles every time the app starts or the
user revisits a symbol.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .contracts import canonical_json
from .desktop_settings import (
    ALPACA_API_KEY_ACCOUNT,
    ALPACA_SECRET_KEY_ACCOUNT,
    load_desktop_settings,
)
from .keychain import KeychainError, KeychainUnavailable, MacOSKeychain


UTC = timezone.utc
ET = ZoneInfo("America/New_York")
CACHE_SCHEMA_VERSION = 1
CACHE_DIRECTORY = "market-cache-v1"
SUPPORTED_TIMEFRAMES: dict[str, int] = {
    "1Min": 60,
    "5Min": 5 * 60,
    "15Min": 15 * 60,
    "1Hour": 60 * 60,
}
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

ProgressCallback = Callable[[float, str, str], None]
CancellationCheck = Callable[[], bool]


class MarketCacheError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Candle timestamp must be finite")
        # Tolerate millisecond and nanosecond epochs from alternate adapters.
        if number > 10**17:
            number /= 1_000_000_000.0
        elif number > 10**14:
            number /= 1_000_000.0
        elif number > 10**11:
            number /= 1_000.0
        parsed = datetime.fromtimestamp(number, tz=UTC)
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Candle timestamp is missing")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _finite_number(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Candle {field} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"Candle {field} must be finite")
    return number


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row.get(name) is not None:
            return row.get(name)
    return None


def normalize_provider_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize Alpaca-style or already-normalized bars into one stable shape."""

    normalized: dict[int, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            stamp = _parse_time(_first(raw, "t", "time", "timestamp", "datetime"))
            opening = _finite_number(_first(raw, "o", "open"), field="open")
            high = _finite_number(_first(raw, "h", "high"), field="high")
            low = _finite_number(_first(raw, "l", "low"), field="low")
            close = _finite_number(_first(raw, "c", "close"), field="close")
            volume = max(0, int(_finite_number(_first(raw, "v", "volume") or 0, field="volume")))
        except (ValueError, TypeError, OSError):
            continue
        if high < low or max(opening, close) > high + 1e-9 or min(opening, close) < low - 1e-9:
            continue
        epoch = int(stamp.timestamp())
        provider_vwap = _first(raw, "vw", "vwap", "provider_vwap")
        trade_count = _first(raw, "n", "trade_count")
        item: dict[str, Any] = {
            "time": epoch,
            "open": round(opening, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
            "volume": volume,
        }
        if provider_vwap is not None:
            try:
                item["provider_vwap"] = round(
                    _finite_number(provider_vwap, field="provider_vwap"),
                    6,
                )
            except ValueError:
                pass
        if trade_count is not None:
            try:
                item["trade_count"] = max(0, int(float(trade_count)))
            except (TypeError, ValueError, OverflowError):
                pass
        normalized[epoch] = item
    return [normalized[key] for key in sorted(normalized)]


def enrich_candles(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add causal features used by the desktop chart and quick-analysis summary."""

    result: list[dict[str, Any]] = []
    ema_9: float | None = None
    atr_14: float | None = None
    prior_close: float | None = None
    session_key: str | None = None
    session_volume = 0.0
    session_price_volume = 0.0
    recent_volumes: list[float] = []
    true_ranges: list[float] = []

    for source in candles:
        row = dict(source)
        opening = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row.get("volume") or 0.0)
        stamp = datetime.fromtimestamp(int(row["time"]), tz=UTC)
        current_session = stamp.astimezone(ET).date().isoformat()
        if current_session != session_key:
            session_key = current_session
            session_volume = 0.0
            session_price_volume = 0.0

        typical = (high + low + close) / 3.0
        session_volume += volume
        session_price_volume += typical * volume
        vwap = (
            session_price_volume / session_volume
            if session_volume > 0
            else close
        )

        alpha = 2.0 / 10.0
        ema_9 = close if ema_9 is None else alpha * close + (1.0 - alpha) * ema_9

        true_range = high - low
        if prior_close is not None:
            true_range = max(
                true_range,
                abs(high - prior_close),
                abs(low - prior_close),
            )
        true_ranges.append(true_range)
        if atr_14 is None:
            if len(true_ranges) >= 14:
                atr_14 = sum(true_ranges[-14:]) / 14.0
        else:
            atr_14 = ((atr_14 * 13.0) + true_range) / 14.0

        previous_average_volume = (
            sum(recent_volumes[-20:]) / min(20, len(recent_volumes))
            if recent_volumes
            else 0.0
        )
        rvol_20 = volume / previous_average_volume if previous_average_volume > 0 else None
        recent_volumes.append(volume)

        row["session"] = current_session
        row["vwap"] = round(vwap, 6)
        row["ema_9"] = round(ema_9, 6)
        row["atr_14"] = round(atr_14, 6) if atr_14 is not None else None
        row["rvol_20"] = round(rvol_20, 4) if rvol_20 is not None else None
        result.append(row)
        prior_close = close
    return result


def analysis_summary(candles: list[dict[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    if not candles:
        raise MarketCacheError("No market-data candles are available for analysis")
    current_time = (now or _utc_now()).astimezone(UTC)
    latest = candles[-1]
    latest_time = datetime.fromtimestamp(int(latest["time"]), tz=UTC)
    latest_session = str(latest.get("session") or "")
    prior_session_close: float | None = None
    for row in reversed(candles[:-1]):
        if str(row.get("session") or "") != latest_session:
            prior_session_close = float(row["close"])
            break
    close = float(latest["close"])
    session_change_pct = (
        ((close / prior_session_close) - 1.0) * 100.0
        if prior_session_close and prior_session_close > 0
        else None
    )
    recent = candles[-20:]
    support = min(float(row["low"]) for row in recent)
    resistance = max(float(row["high"]) for row in recent)
    vwap = float(latest.get("vwap") or close)
    ema_9 = float(latest.get("ema_9") or close)
    atr = latest.get("atr_14")
    atr_value = float(atr) if atr is not None else None
    return {
        "latest_bar_close": round(close, 6),
        "as_of": _iso(latest_time),
        "data_age_seconds": max(0.0, round((current_time - latest_time).total_seconds(), 1)),
        "session_change_pct": round(session_change_pct, 3) if session_change_pct is not None else None,
        "vwap": round(vwap, 6),
        "ema_9": round(ema_9, 6),
        "atr_14": round(atr_value, 6) if atr_value is not None else None,
        "atr_pct": (
            round((atr_value / close) * 100.0, 3)
            if atr_value is not None and close > 0
            else None
        ),
        "rvol_20": latest.get("rvol_20"),
        "distance_from_vwap_pct": round(((close / vwap) - 1.0) * 100.0, 3) if vwap > 0 else None,
        "distance_from_ema9_pct": round(((close / ema_9) - 1.0) * 100.0, 3) if ema_9 > 0 else None,
        "above_vwap": close >= vwap,
        "above_ema9": close >= ema_9,
        "support_20": round(support, 6),
        "resistance_20": round(resistance, 6),
    }


class PersistentMarketDataCache:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(data_dir).expanduser().resolve() / CACHE_DIRECTORY
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clean_key(symbol: str, timeframe: str, feed: str, adjustment: str) -> tuple[str, str, str, str]:
        clean_symbol = str(symbol or "").strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(clean_symbol):
            raise MarketCacheError("Enter one valid stock ticker")
        clean_timeframe = str(timeframe or "5Min").strip()
        if clean_timeframe not in SUPPORTED_TIMEFRAMES:
            raise MarketCacheError("Choose 1Min, 5Min, 15Min, or 1Hour candles")
        clean_feed = str(feed or "sip").strip().lower()
        if clean_feed not in {"sip", "iex"}:
            raise MarketCacheError("Market-data feed must be SIP or IEX")
        clean_adjustment = str(adjustment or "split").strip().lower()
        if clean_adjustment not in {"raw", "split", "dividend", "all"}:
            raise MarketCacheError("Unsupported market-data adjustment")
        return clean_symbol, clean_timeframe, clean_feed, clean_adjustment

    def _path(self, symbol: str, timeframe: str, feed: str, adjustment: str) -> Path:
        material = f"{symbol}|{timeframe}|{feed}|{adjustment}|v{CACHE_SCHEMA_VERSION}"
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return self.root / f"{symbol}-{timeframe}-{feed}-{digest}.json.gz"

    def load(self, symbol: str, timeframe: str, feed: str, adjustment: str = "split") -> dict[str, Any] | None:
        symbol, timeframe, feed, adjustment = self._clean_key(
            symbol, timeframe, feed, adjustment
        )
        path = self._path(symbol, timeframe, feed, adjustment)
        if not path.is_file():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                decoded = json.load(handle)
        except (OSError, json.JSONDecodeError, EOFError):
            return None
        if not isinstance(decoded, dict):
            return None
        expected = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "feed": feed,
            "adjustment": adjustment,
        }
        if any(decoded.get(key) != value for key, value in expected.items()):
            return None
        candles = decoded.get("candles")
        if not isinstance(candles, list):
            return None
        decoded["candles"] = [dict(row) for row in candles if isinstance(row, dict)]
        decoded["artifact_path"] = str(path)
        return decoded

    def save(
        self,
        *,
        symbol: str,
        timeframe: str,
        feed: str,
        adjustment: str,
        candles: list[dict[str, Any]],
        refreshed_at: datetime,
    ) -> dict[str, Any]:
        symbol, timeframe, feed, adjustment = self._clean_key(
            symbol, timeframe, feed, adjustment
        )
        path = self._path(symbol, timeframe, feed, adjustment)
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "symbol": symbol,
            "timeframe": timeframe,
            "feed": feed,
            "adjustment": adjustment,
            "refreshed_at": _iso(refreshed_at),
            "candles": candles,
        }
        payload["fingerprint"] = hashlib.sha256(
            canonical_json(
                {
                    "key": [symbol, timeframe, feed, adjustment],
                    "bars": len(candles),
                    "first": candles[0]["time"] if candles else None,
                    "last": candles[-1]["time"] if candles else None,
                }
            ).encode("utf-8")
        ).hexdigest()
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        os.close(fd)
        temporary = Path(temp_name)
        try:
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
                json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        result = dict(payload)
        result["artifact_path"] = str(path)
        return result

    def refresh(
        self,
        provider: Any,
        *,
        symbol: str,
        timeframe: str = "5Min",
        feed: str = "sip",
        adjustment: str = "split",
        history_days: int = 20,
        max_cache_age_seconds: int = 20,
        now: datetime | None = None,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCheck | None = None,
    ) -> dict[str, Any]:
        symbol, timeframe, feed, adjustment = self._clean_key(
            symbol, timeframe, feed, adjustment
        )
        current_time = (now or _utc_now()).astimezone(UTC)
        history_days = max(2, min(120, int(history_days)))
        max_cache_age_seconds = max(0, min(300, int(max_cache_age_seconds)))
        cached = self.load(symbol, timeframe, feed, adjustment)
        cached_candles = list((cached or {}).get("candles") or [])

        if cancelled and cancelled():
            raise MarketCacheError("Market-data refresh was cancelled")
        if cached_candles and cached and max_cache_age_seconds > 0:
            try:
                refreshed_at = _parse_time(cached.get("refreshed_at"))
            except ValueError:
                refreshed_at = datetime.fromtimestamp(0, tz=UTC)
            if (current_time - refreshed_at).total_seconds() <= max_cache_age_seconds:
                enriched = enrich_candles(cached_candles)
                result = dict(cached)
                result["candles"] = enriched
                result["cache_hit"] = True
                result["network_request"] = False
                result["provider_rows"] = 0
                result["summary"] = analysis_summary(enriched, now=current_time)
                return result

        history_start = current_time - timedelta(days=history_days)
        request_start = history_start
        if cached_candles:
            last_time = datetime.fromtimestamp(int(cached_candles[-1]["time"]), tz=UTC)
            overlap = timedelta(seconds=SUPPORTED_TIMEFRAMES[timeframe] * 3)
            request_start = max(history_start, last_time - overlap)
        if progress:
            progress(
                0.18,
                "downloading_data",
                (
                    "Refreshing only candles newer than the persistent cache"
                    if cached_candles
                    else "Downloading the initial candle history"
                ),
            )
        if cancelled and cancelled():
            raise MarketCacheError("Market-data refresh was cancelled")

        response = provider.bars(
            [symbol],
            start=request_start,
            end=current_time,
            timeframe=timeframe,
            feed=feed,
            adjustment=adjustment,
            max_pages=25,
        )
        provider_rows = response.get(symbol) if isinstance(response, Mapping) else None
        normalized_new = normalize_provider_bars(
            [dict(row) for row in provider_rows or [] if isinstance(row, Mapping)]
        )
        merged: dict[int, dict[str, Any]] = {
            int(row["time"]): dict(row)
            for row in cached_candles
            if isinstance(row, Mapping) and row.get("time") is not None
        }
        for row in normalized_new:
            merged[int(row["time"])] = row
        cutoff = int(history_start.timestamp())
        merged_rows = [merged[key] for key in sorted(merged) if key >= cutoff]
        if not merged_rows:
            raise MarketCacheError(
                f"No {timeframe} candles were returned for {symbol}. Check the symbol, feed, and Alpaca permissions."
            )
        if progress:
            progress(0.68, "preparing_features", "Updating VWAP, EMA 9, ATR 14, and RVOL 20")
        if cancelled and cancelled():
            raise MarketCacheError("Market-data refresh was cancelled")
        enriched = enrich_candles(merged_rows)
        saved = self.save(
            symbol=symbol,
            timeframe=timeframe,
            feed=feed,
            adjustment=adjustment,
            candles=enriched,
            refreshed_at=current_time,
        )
        saved["cache_hit"] = bool(cached_candles)
        saved["network_request"] = True
        saved["provider_rows"] = len(normalized_new)
        saved["incremental_start"] = _iso(request_start)
        saved["summary"] = analysis_summary(enriched, now=current_time)
        return saved


def load_alpaca_credentials() -> tuple[str, str]:
    api_key = str(os.environ.get("ALPACA_API_KEY") or "").strip()
    secret_key = str(os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    if api_key and secret_key:
        return api_key, secret_key
    try:
        keychain = MacOSKeychain()
        if not api_key:
            api_key = keychain.get_secret(ALPACA_API_KEY_ACCOUNT).strip()
        if not secret_key:
            secret_key = keychain.get_secret(ALPACA_SECRET_KEY_ACCOUNT).strip()
    except (KeychainError, KeychainUnavailable):
        pass
    if not api_key or not secret_key:
        raise MarketCacheError(
            "Add the Alpaca API key and secret in Connection Settings before running real market analysis."
        )
    return api_key, secret_key


def run_stock_analysis(
    payload: Mapping[str, Any],
    *,
    data_dir: str | Path,
    progress: ProgressCallback,
    cancelled: CancellationCheck,
) -> dict[str, Any]:
    symbol = str(payload.get("symbol") or "").strip().upper()
    timeframe = str(payload.get("timeframe") or "5Min").strip()
    settings = load_desktop_settings(data_dir)
    feed = str(payload.get("feed") or settings.market_feed or "sip").strip().lower()
    history_days = max(2, min(120, int(payload.get("history_days") or 20)))
    max_cache_age_seconds = max(
        0,
        min(300, int(payload.get("max_cache_age_seconds") or 20)),
    )
    progress(0.05, "preparing_features", "Loading secure Alpaca credentials")
    api_key, secret_key = load_alpaca_credentials()
    if cancelled():
        raise MarketCacheError("Stock analysis was cancelled")
    from youtube_strategy_engine import AlpacaMarketData

    provider = AlpacaMarketData(
        api_key,
        secret_key,
        live_feed=feed,
        historical_feed=feed,
    )
    cache = PersistentMarketDataCache(data_dir)
    refreshed = cache.refresh(
        provider,
        symbol=symbol,
        timeframe=timeframe,
        feed=feed,
        history_days=history_days,
        max_cache_age_seconds=max_cache_age_seconds,
        progress=progress,
        cancelled=cancelled,
    )
    progress(0.9, "saving", "Preparing the local analysis result")
    candles = list(refreshed.get("candles") or [])
    return {
        "status": "ok",
        "symbol": symbol,
        "timeframe": timeframe,
        "feed": feed,
        "bars": len(candles),
        "candles": candles,
        "summary": dict(refreshed.get("summary") or {}),
        "cache": {
            "hit": bool(refreshed.get("cache_hit")),
            "network_request": bool(refreshed.get("network_request")),
            "provider_rows": int(refreshed.get("provider_rows") or 0),
            "fingerprint": str(refreshed.get("fingerprint") or ""),
            "refreshed_at": str(refreshed.get("refreshed_at") or ""),
            "incremental_start": str(refreshed.get("incremental_start") or ""),
        },
        "research_only": True,
        "affects_execution": False,
        "price_label": "Latest completed Alpaca candle close",
    }
