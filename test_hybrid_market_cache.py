from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest

from hybrid_runtime.desktop_settings import DesktopSettings, DesktopSettingsError
from hybrid_runtime.engine_adapter import default_handlers
from hybrid_runtime.market_cache import (
    PersistentMarketDataCache,
    analysis_summary,
    enrich_candles,
    normalize_provider_bars,
)


UTC = timezone.utc


def provider_row(stamp: datetime, close: float, volume: int = 1000) -> dict:
    return {
        "t": stamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "o": close - 0.05,
        "h": close + 0.12,
        "l": close - 0.11,
        "c": close,
        "v": volume,
        "n": 42,
        "vw": close - 0.01,
    }


class FakeProvider:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = list(rows)
        self.calls: list[dict] = []

    def bars(self, symbols, **kwargs):
        self.calls.append({"symbols": list(symbols), **kwargs})
        start = kwargs["start"]
        end = kwargs["end"]
        selected = []
        for row in self.rows:
            stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
            if start <= stamp <= end:
                selected.append(dict(row))
        return {symbols[0]: selected}


def noop_progress(_fraction: float, _stage: str, _message: str) -> None:
    return None


def never_cancelled() -> bool:
    return False


def test_normalize_provider_bars_accepts_alpaca_shape_and_dedupes():
    stamp = datetime(2026, 9, 1, 14, 30, tzinfo=UTC)
    rows = [provider_row(stamp, 10.0), provider_row(stamp, 10.2)]
    normalized = normalize_provider_bars(rows)
    assert len(normalized) == 1
    assert normalized[0]["close"] == 10.2
    assert normalized[0]["trade_count"] == 42
    assert normalized[0]["provider_vwap"] == 10.19


def test_enrich_candles_is_causal_and_resets_vwap_by_session():
    first = datetime(2026, 9, 1, 19, 55, tzinfo=UTC)
    raw = normalize_provider_bars(
        [
            provider_row(first + timedelta(minutes=index * 5), 10 + index * 0.1, 1000 + index * 20)
            for index in range(14)
        ]
        + [
            provider_row(datetime(2026, 9, 2, 13, 30, tzinfo=UTC), 12.0, 2000),
            provider_row(datetime(2026, 9, 2, 13, 35, tzinfo=UTC), 12.2, 2100),
        ]
    )
    enriched = enrich_candles(raw)
    assert enriched[-1]["ema_9"] is not None
    assert enriched[-1]["atr_14"] is not None
    assert enriched[-1]["rvol_20"] is not None
    assert enriched[-2]["session"] != enriched[-3]["session"]
    # First candle of the new session has session VWAP equal to its typical price.
    new_session = enriched[-2]
    expected = (new_session["high"] + new_session["low"] + new_session["close"]) / 3.0
    assert new_session["vwap"] == pytest.approx(expected, abs=1e-6)


def test_persistent_cache_reuses_fresh_artifact_without_network():
    now = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
    rows = [
        provider_row(now - timedelta(minutes=5 * (30 - index)), 20 + index * 0.03)
        for index in range(30)
    ]
    provider = FakeProvider(rows)
    with tempfile.TemporaryDirectory() as temporary:
        cache = PersistentMarketDataCache(temporary)
        first = cache.refresh(
            provider,
            symbol="SDOT",
            timeframe="5Min",
            feed="sip",
            history_days=2,
            max_cache_age_seconds=20,
            now=now,
            progress=noop_progress,
            cancelled=never_cancelled,
        )
        assert first["network_request"] is True
        assert len(provider.calls) == 1
        assert Path(first["artifact_path"]).is_file()

        # Reconstruct the cache object to prove persistence across app restarts.
        restarted_cache = PersistentMarketDataCache(temporary)
        second = restarted_cache.refresh(
            provider,
            symbol="SDOT",
            timeframe="5Min",
            feed="sip",
            history_days=2,
            max_cache_age_seconds=20,
            now=now + timedelta(seconds=10),
            progress=noop_progress,
            cancelled=never_cancelled,
        )
        assert second["cache_hit"] is True
        assert second["network_request"] is False
        assert len(provider.calls) == 1
        assert second["summary"]["latest_bar_close"] == pytest.approx(rows[-1]["c"])


def test_incremental_refresh_requests_only_small_overlap_after_cached_tail():
    now = datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
    initial = [
        provider_row(now - timedelta(minutes=5 * (12 - index)), 30 + index * 0.04)
        for index in range(12)
    ]
    provider = FakeProvider(initial)
    with tempfile.TemporaryDirectory() as temporary:
        cache = PersistentMarketDataCache(temporary)
        first = cache.refresh(
            provider,
            symbol="AAPL",
            timeframe="5Min",
            feed="iex",
            history_days=2,
            max_cache_age_seconds=0,
            now=now,
            progress=noop_progress,
            cancelled=never_cancelled,
        )
        cached_last = datetime.fromtimestamp(first["candles"][-1]["time"], tz=UTC)

        later = now + timedelta(minutes=10)
        provider.rows.extend(
            [
                provider_row(now + timedelta(minutes=5), 30.6),
                provider_row(now + timedelta(minutes=10), 30.8),
            ]
        )
        second = cache.refresh(
            provider,
            symbol="AAPL",
            timeframe="5Min",
            feed="iex",
            history_days=2,
            max_cache_age_seconds=0,
            now=later,
            progress=noop_progress,
            cancelled=never_cancelled,
        )
        assert len(provider.calls) == 2
        requested_start = provider.calls[-1]["start"]
        assert requested_start == cached_last - timedelta(minutes=15)
        assert second["candles"][-1]["close"] == pytest.approx(30.8)
        assert second["provider_rows"] <= 6


def test_analysis_summary_labels_age_and_structure_without_trade_signal():
    now = datetime(2026, 9, 2, 16, 0, tzinfo=UTC)
    candles = enrich_candles(
        normalize_provider_bars(
            [
                provider_row(now - timedelta(minutes=5 * (25 - index)), 15 + index * 0.02)
                for index in range(25)
            ]
        )
    )
    summary = analysis_summary(candles, now=now)
    assert summary["latest_bar_close"] > 0
    assert summary["data_age_seconds"] >= 0
    assert summary["support_20"] <= summary["latest_bar_close"] <= summary["resistance_20"]
    assert isinstance(summary["above_vwap"], bool)


def test_desktop_market_feed_is_non_secret_and_validated():
    assert DesktopSettings(market_feed="SIP").market_feed == "sip"
    assert DesktopSettings.from_mapping({"market_feed": "iex"}).market_feed == "iex"
    with pytest.raises(DesktopSettingsError):
        DesktopSettings(market_feed="other")


def test_real_analysis_job_is_registered_locally():
    assert "analysis.stock" in default_handlers()
