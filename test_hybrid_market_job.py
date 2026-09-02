from __future__ import annotations

from hybrid_runtime import market_job


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
