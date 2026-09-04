from __future__ import annotations

import pytest

from alpaca_iex_momentum import IexCompatibleAlpacaMarketData
from youtube_strategy_engine import AlpacaMarketData, AppError


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


def test_snapshot_401_retries_once_with_paper_credentials(monkeypatch):
    market = IexCompatibleAlpacaMarketData(
        "stale-key",
        "stale-secret",
        live_feed="iex",
        fallback_api_key="paper-key",
        fallback_secret_key="paper-secret",
    )
    calls: list[str] = []

    def fake_parent_get(self, path, parameters=None):
        key = self.headers["APCA-API-KEY-ID"]
        calls.append(key)
        if key == "stale-key":
            raise AppError(
                "Request was denied (401). <html><title>401 Authorization Required</title></html>"
            )
        assert key == "paper-key"
        return {"AAA": _snapshot(12.0, 10.0, 1_000.0)}

    monkeypatch.setattr(AlpacaMarketData, "_get", fake_parent_get)

    snapshots = market.snapshots(["AAA"])

    assert snapshots["AAA"]["latestTrade"]["p"] == 12.0
    assert calls == ["stale-key", "paper-key"]
    assert market.headers["APCA-API-KEY-ID"] == "paper-key"
    assert market._using_fallback_credentials is True


def test_snapshot_final_401_is_sanitized(monkeypatch):
    market = IexCompatibleAlpacaMarketData("stale-key", "stale-secret", live_feed="iex")

    def fake_parent_get(self, path, parameters=None):
        raise AppError(
            "Request was denied (401). <html><head><title>401 Authorization Required</title></head>"
            "<body><center><h1>401 Authorization Required</h1></center><center>nginx</center></body></html>"
        )

    monkeypatch.setattr(AlpacaMarketData, "_get", fake_parent_get)

    with pytest.raises(AppError) as exc_info:
        market.snapshots(["AAA"])

    message = str(exc_info.value)
    assert "Alpaca authentication failed" in message
    assert "Streamlit Secrets" in message
    assert "<html>" not in message
    assert "nginx" not in message


def test_paper_credentials_can_be_the_only_configured_pair(monkeypatch):
    market = IexCompatibleAlpacaMarketData(
        "",
        "",
        live_feed="iex",
        fallback_api_key="paper-key",
        fallback_secret_key="paper-secret",
    )

    def fake_parent_get(self, path, parameters=None):
        assert self.headers["APCA-API-KEY-ID"] == "paper-key"
        return {"AAA": _snapshot(3.0, 2.5, 2_000.0)}

    monkeypatch.setattr(AlpacaMarketData, "_get", fake_parent_get)

    assert market.snapshots(["AAA"])["AAA"]["latestTrade"]["p"] == 3.0
