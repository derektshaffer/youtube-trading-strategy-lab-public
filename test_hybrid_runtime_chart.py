from __future__ import annotations

from hybrid_runtime.contracts import ExecutionTarget, JobRequest
from hybrid_runtime.engine_adapter import (
    chart_framework_fixture_handler,
    system_health_handler,
)
from hybrid_runtime.router import RoutingPolicy


def test_framework_chart_fixture_is_deterministic_and_valid_ohlcv():
    progress: list[tuple[float, str, str]] = []

    def report(fraction: float, stage: str, message: str) -> None:
        progress.append((fraction, stage, message))

    payload = {"symbol": "sdot", "timeframe": "5Min", "bars": 220}
    first = chart_framework_fixture_handler(payload, report, lambda: False)
    second = chart_framework_fixture_handler(payload, lambda *_: None, lambda: False)

    assert first == second
    assert first["synthetic"] is True
    assert first["symbol"] == "SDOT"
    assert first["timeframe"] == "5Min"
    assert first["bars"] == 220
    candles = first["candles"]
    assert len(candles) == 220
    assert [stage for _, stage, _ in progress] == [
        "downloading_data",
        "preparing_features",
        "saving",
    ]
    previous_time = 0
    for candle in candles:
        assert candle["time"] > previous_time
        previous_time = candle["time"]
        assert candle["low"] <= candle["open"] <= candle["high"]
        assert candle["low"] <= candle["close"] <= candle["high"]
        assert candle["volume"] > 0
        assert candle["vwap"] > 0
        assert candle["ema_9"] > 0


def test_framework_chart_fixture_routes_local_without_heavy_signals():
    decision = RoutingPolicy().decide(
        JobRequest(
            "chart.framework_fixture",
            {"symbol": "SDOT", "timeframe": "15Min", "bars": 220},
        )
    )
    assert decision.target == ExecutionTarget.LOCAL
    assert decision.automatic is True
    assert decision.heavy_signals == ()


def test_health_handler_echoes_only_explicit_client_measurements():
    result = system_health_handler(
        {
            "checks": ["chart-rendered"],
            "client_metrics": {
                "framework": "pyside6",
                "chart_render_ms": 2.5,
            },
        },
        lambda *_: None,
        lambda: False,
    )
    assert result["requested_checks"] == ["chart-rendered"]
    assert result["client_metrics"] == {
        "framework": "pyside6",
        "chart_render_ms": 2.5,
    }
