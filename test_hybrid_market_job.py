from __future__ import annotations

from types import SimpleNamespace

from hybrid_runtime.contracts import JobRequest, JobStatus
from hybrid_runtime.engine_adapter import stock_analysis_handler
from hybrid_runtime import market_cache
from hybrid_runtime import market_job
from hybrid_runtime.service import HybridService
from hybrid_runtime.storage import HybridStore
from hybrid_runtime.worker import LocalWorker


def test_durable_market_job_keeps_full_history_out_of_sqlite(monkeypatch, tmp_path):
    candles = [
        {
            "time": index,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "volume": 100,
            "vwap": 1.0,
            "ema_9": 1.0,
        }
        for index in range(2_000)
    ]

    def fake_run(*_args, **_kwargs):
        return {
            "status": "ok",
            "symbol": "TEST",
            "candles": candles,
            "bars": len(candles),
            "summary": {"latest_bar_close": 1.0},
        }

    monkeypatch.setattr(market_job, "run_stock_analysis", fake_run)
    result = market_job.run_bounded_stock_analysis(
        {"symbol": "TEST"},
        data_dir=tmp_path,
        progress=lambda *_args: None,
        cancelled=lambda: False,
    )

    assert result["cached_bars"] == 2_000
    assert result["chart_bars"] == market_job.MAX_CHART_CANDLES
    assert len(result["candles"]) == market_job.MAX_CHART_CANDLES
    assert result["candles"][0]["time"] == 1_400
    assert result["candles"][-1]["time"] == 1_999


def test_stock_analysis_durable_stages_do_not_rewind(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_INTELLIGENCE_DESKTOP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        market_cache,
        "load_desktop_settings",
        lambda _data_dir: SimpleNamespace(market_feed="iex"),
    )
    monkeypatch.setattr(
        market_cache,
        "load_alpaca_credentials",
        lambda: ("test-api-key", "test-secret-key"),
    )

    import youtube_strategy_engine

    monkeypatch.setattr(
        youtube_strategy_engine,
        "AlpacaMarketData",
        lambda *_args, **_kwargs: object(),
    )

    def fake_refresh(
        _cache,
        _provider,
        *,
        progress,
        **_kwargs,
    ):
        progress(0.18, "downloading_data", "Downloading the initial candle history")
        progress(0.68, "preparing_features", "Updating indicators")
        return {
            "candles": [],
            "summary": {},
            "cache_hit": False,
            "network_request": True,
            "provider_rows": 0,
        }

    monkeypatch.setattr(market_cache.PersistentMarketDataCache, "refresh", fake_refresh)

    service = HybridService(HybridStore(tmp_path / "hybrid.sqlite3"))
    job, created = service.submit(JobRequest("analysis.stock", {"symbol": "AAPL"}))
    assert created is True
    worker = LocalWorker(
        service,
        worker_id="stock-analysis-test-worker",
        handlers={"analysis.stock": stock_analysis_handler},
    )

    assert worker.run_once() is True
    finished = service.get(job.id)
    assert finished.status == JobStatus.COMPLETE
    assert finished.result["research_only"] is True
    assert finished.result["affects_execution"] is False
    assert [event["status"] for event in service.events(job.id)] == [
        "queued",
        "claimed",
        "downloading_data",
        "downloading_data",
        "preparing_features",
        "saving",
        "complete",
    ]
