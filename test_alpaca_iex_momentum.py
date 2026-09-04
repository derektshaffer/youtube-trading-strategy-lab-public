from __future__ import annotations

import pytest

from alpaca_iex_momentum import IexCompatibleAlpacaMarketData
from youtube_strategy_engine import AppError


def _snapshot(close: float, previous: float, volume: float) -> dict:
    return {
        "dailyBar": {"c": close, "v": volume},
        "prevDailyBar": {"c": previous},
        "latestTrade": {"p": close},
    }


def test_iex_momentum_ranks_gainers_and_activity_and_reuses_cache():
    market = IexCompatibleAlpacaMarketData("key", "secret", live_feed="iex")
    market.equity_catalog = lambda status=None: [
        {"symbol": "AAA", "class": "us_equity", "tradable": True},
        {"symbol": "BBB", "class": "us_equity", "tradable": True},
        {"symbol": "CCC", "class": "us_equity", "tradable": True},
        {"symbol": "BAD1", "class": "us_equity", "tradable": True},
        {"symbol": "NOPE", "class": "us_equity", "tradable": False},
    ]

    calls = []

    def snapshots(symbols):
        calls.append(list(symbols))
        payload = {
            "AAA": _snapshot(12.0, 10.0, 100.0),
            "BBB": _snapshot(22.0, 20.0, 1_000.0),
            "CCC": _snapshot(4.0, 5.0, 5_000.0),
        }
        return {symbol: payload[symbol] for symbol in symbols if symbol in payload}

    market.snapshots = snapshots

    assert market.movers(top=3) == ["AAA", "BBB", "CCC"]
    assert market.most_active(top=3) == ["CCC", "BBB", "AAA"]
    assert calls == [["AAA", "BBB", "CCC"]]


def test_iex_momentum_sanitizes_401_html_errors():
    market = IexCompatibleAlpacaMarketData("key", "secret", live_feed="iex")

    def denied(status=None):
        raise AppError(
            "Request was denied (401). <html><head><title>401 Authorization Required</title></head></html>"
        )

    market.equity_catalog = denied

    with pytest.raises(AppError) as exc_info:
        market.movers(top=10)

    message = str(exc_info.value)
    assert "Alpaca authentication failed" in message
    assert "ALPACA_API_KEY" in message
    assert "<html>" not in message


def test_iex_momentum_accepts_snake_case_snapshot_shape():
    market = IexCompatibleAlpacaMarketData("key", "secret", live_feed="iex")
    market.equity_catalog = lambda status=None: [
        {"symbol": "XYZ", "asset_class": "us_equity", "tradable": True},
    ]
    market.snapshots = lambda symbols: {
        "XYZ": {
            "daily_bar": {"c": 2.0, "v": 25_000.0},
            "previous_daily_bar": {"c": 1.0},
            "latest_trade": {"p": 2.0},
        }
    }

    assert market.movers(top=1) == ["XYZ"]
    assert market.most_active(top=1) == ["XYZ"]
